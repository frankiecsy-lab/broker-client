"""M3 offscreen smoke test — test-matrix state machine, zero broker contact.

Run:  QT_QPA_PLATFORM=offscreen python _smoke_m3.py
Exits non-zero on any assertion failure; prints SMOKE_M3_OK at the end.

Uses a fake bridge (FakeLoopThread + FakeWorker) so every state transition is
deterministic and no futu/ib_async module is ever imported:
  A. default 48-row matrix renders with the bridge NOT ready (Run disabled,
     broker SDKs absent from sys.modules)
  B. worker_ready -> single Run: pending -> running -> pass; preview table +
     meta line render in the row body
  C. Test All: progress reaches total/total, summary counts correct,
     batch_finished fires exactly once
  D. Abort: a hanging stream row becomes aborted and cancel_all is forwarded
  E. strict OFF folds ok_no_ticks into pass
  F. unchecking every ktype empties the matrix (empty label shown), restoring
     brings the 48 rows back
  G. stream live ticks (fake worker emits baseline + 2 ticks) drive the row's
     sparkline and tick counter before the final result lands
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, QEventLoop, QTimer, Signal  # noqa: E402
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
    timer.setInterval(20)
    timer.timeout.connect(check)
    timer.start()
    QTimer.singleShot(int(timeout_s * 1000), loop.quit)
    check()
    loop.exec()
    return state["ok"]


class FakeWorker(QObject):
    """Mimics the AsyncWorker signal surface consumed by TestEngine."""

    test_started = Signal(str)
    test_finished = Signal(object)
    stream_tick = Signal(str, object)


class FakeLoopThread(QObject):
    """Mimics the LoopThread facade surface used by page/engine.

    mode 'fast' finishes tests after a short timer; mode 'hang' emits
    test_started and never finishes (for the abort test).
    """

    worker_ready = Signal(object)

    def __init__(self, worker=None):
        super().__init__()
        self.worker = worker
        self.mode = "fast"
        self.stream_status = "pass"  # 'ok_no_ticks' for section E
        self.calls: list[tuple] = []
        self.cancelled = 0

    def run_get_kline(self, test_id, code, ktype, broker, kline_num):
        self.calls.append(("get", test_id))
        w = self.worker
        w.test_started.emit(test_id)
        if self.mode == "hang":
            return
        QTimer.singleShot(20, lambda: w.test_finished.emit(self._get_result(test_id)))

    def run_stream(self, test_id, code, ktype, broker, kline_num, duration_s):
        self.calls.append(("stream", test_id))
        w = self.worker
        w.test_started.emit(test_id)
        if self.mode == "hang":
            return
        # baseline tick (history snapshot), then two live ticks before the window ends
        base_closes = [100.0 + i * 0.5 for i in range(20)]
        bar = {"time_key": "2026-10-05T09:30:00", "open": 100.0, "high": 101.0,
               "low": 99.5, "close": 100.0, "volume": 500}
        w.stream_tick.emit(test_id, {"ticks": 0, "last_bar": bar, "closes": list(base_closes)})

        def live(i):
            closes = base_closes + [101.0 + i]
            w.stream_tick.emit(test_id, {
                "ticks": i,
                "last_bar": {"time_key": f"2026-10-05T09:30:{i:02d}", "open": 101.0,
                             "high": 102.0, "low": 100.5, "close": 101.0 + i, "volume": 60},
                "closes": closes,
            })

        QTimer.singleShot(30, lambda: live(1))
        QTimer.singleShot(60, lambda: live(2))
        QTimer.singleShot(80, lambda: w.test_finished.emit(self._stream_result(test_id)))

    def run_batch(self, specs):
        self.calls.append(("batch", len(specs)))
        for p in specs:
            if p["func"] == "stream_kline":
                self.run_stream(p["test_id"], p["code"], p["ktype"], p["broker"],
                                p["kline_num"], p["duration_s"])
            else:
                self.run_get_kline(p["test_id"], p["code"], p["ktype"], p["broker"],
                                   p["kline_num"])

    def cancel_all(self):
        self.cancelled += 1

    @staticmethod
    def _get_result(test_id, rows=20):
        preview = [
            {"time_key": f"2026-10-0{i % 9 + 1}T09:30:00", "open": 100 + i,
             "high": 101 + i, "low": 99 + i, "close": 100.5 + i, "volume": 1000 * i}
            for i in range(rows)
        ]
        return {
            "test_id": test_id, "code": "?", "ktype": "?", "broker": "?",
            "func": "get_kline", "status": "pass", "rows": 100, "elapsed_ms": 42.0,
            "preview": preview, "last_bar": preview[-1],
        }

    def _stream_result(self, test_id):
        return {
            "test_id": test_id, "code": "?", "ktype": "?", "broker": "?",
            "func": "stream_kline", "status": self.stream_status, "elapsed_ms": 3000.0,
            "baseline": True, "ticks": 7 if self.stream_status == "pass" else 0,
            "first_ts": "2026-10-05T09:30:00", "last_ts": "2026-10-05T09:30:29",
            "closes": [100.0, 100.5, 101.0],
            "last_bar": {"time_key": "2026-10-05T09:30:29", "open": 1.0, "high": 2.0,
                         "low": 0.5, "close": 1.5, "volume": 9},
        }


def main() -> int:
    app = QApplication(sys.argv)

    from app.context import AppContext
    from app.i18n import Translator
    from app.settings import AppSettings
    from app.theme.manager import ThemeManager
    from app.testtool.page import TestToolPage

    settings = AppSettings()
    # pin persisted test-tool defaults so matrix/param assertions stay deterministic
    settings.tt_codes = ["US.AAPL", "HK.00700"]
    settings.tt_kline_num = 100
    settings.tt_duration = 30
    settings.tt_strict = True
    settings.tt_mock = False
    translator = Translator(settings.language)
    theme_mgr = ThemeManager(app, settings, initial=settings.theme)
    t = translator.t

    # --- A. 48-row matrix with the bridge NOT ready ---------------------------------
    fake_lt = FakeLoopThread(worker=None)
    ctx = AppContext(
        settings=settings, translator=translator, theme_mgr=theme_mgr,
        worker=fake_lt, broker_dir="",
    )
    page = TestToolPage(ctx)
    page.show()
    app.processEvents()

    assert len(page._rows) == 48, f"expected 48 rows, got {len(page._rows)}"
    for row in page._rows.values():
        assert row.status_pill.text() == t("testtool.status_pending")
    assert not page.btn_test_all.isEnabled(), "Test All must be disabled pre-ready"
    for row in page._rows.values():
        assert not row.run_btn.isEnabled(), "Run must be disabled pre-ready"
    # zero broker contact: the broker SDKs must never have been imported
    banned = [m for m in ("futu", "ib_async", "broker") if m in sys.modules]
    assert not banned, f"broker modules imported during page build: {banned}"

    # --- B. worker_ready -> single Run (get_kline) -----------------------------------
    fake_worker = FakeWorker()
    fake_lt.worker = fake_worker  # real LoopThread sets .worker before emitting ready
    fake_lt.worker_ready.emit(fake_worker)  # direct emit, same thread
    app.processEvents()
    assert page.btn_test_all.isEnabled(), "Test All must enable on worker_ready"
    for row in page._rows.values():
        assert row.run_btn.isEnabled(), "Run buttons must enable on worker_ready"

    target = next(r for r in page._rows.values() if r.spec.func == "get_kline")
    tid = target.spec.test_id
    target.run_btn.click()
    app.processEvents()
    ok = wait_for(lambda: page.engine.state_for(tid)["status"] == "pass", timeout_s=5)
    assert ok, f"single run did not reach pass: {page.engine.state_for(tid)}"
    st = page.engine.state_for(tid)
    assert st["rows"] == 100 and st["elapsed_ms"] > 0

    target.set_expanded(True)
    app.processEvents()
    assert target.table.rowCount() == 20, "preview should show up to 20 rows"
    assert "42" in target.meta_label.text(), f"meta missing elapsed: {target.meta_label.text()!r}"

    # --- C. Test All: progress + counts + batch_finished ------------------------------
    finished_summaries = []
    progress_seen = []
    page.engine.batch_finished.connect(lambda s: finished_summaries.append(s))
    page.engine.batch_progress.connect(lambda d, m: progress_seen.append((d, m)))

    page.btn_test_all.click()
    app.processEvents()
    ok = wait_for(lambda: len(finished_summaries) == 1, timeout_s=30)
    assert ok, "batch_finished never fired"
    summary = finished_summaries[0]
    assert summary["pass"] == 48 and summary["fail"] == 0, summary
    assert progress_seen[-1] == (48, 48), f"progress did not reach total: {progress_seen[-3:]}"
    assert len(progress_seen) >= 2, "no intermediate progress emissions"
    assert page.progress.value() == 48 and page.progress.maximum() == 48
    assert t("testtool.summary_pass") in page.summary_label.text(), (
        f"summary label: {page.summary_label.text()!r}"
    )

    # --- D. Abort a hanging stream ------------------------------------------------------
    fake_lt.mode = "hang"
    stream_row = next(r for r in page._rows.values() if r.spec.func == "stream_kline")
    sid = stream_row.spec.test_id
    stream_row.run_btn.click()
    app.processEvents()
    assert page.engine.state_for(sid)["status"] == "running"
    assert page.btn_abort.isEnabled(), "Abort must be enabled while a test runs"
    page.btn_abort.click()  # -> engine.abort()
    app.processEvents()
    assert page.engine.state_for(sid)["status"] == "aborted", (
        f"abort did not mark row aborted: {page.engine.state_for(sid)}"
    )
    assert fake_lt.cancelled >= 1, "cancel_all not forwarded to the bridge"

    # --- E. strict OFF folds ok_no_ticks into pass ---------------------------------------
    fake_lt.mode = "fast"
    page.engine.set_strict(False)
    fake_lt.stream_status = "ok_no_ticks"
    row2 = next(
        r for r in page._rows.values()
        if r.spec.func == "stream_kline" and r.spec.test_id != sid
    )
    tid2 = row2.spec.test_id
    row2.run_btn.click()
    ok = wait_for(lambda: page.engine.state_for(tid2)["status"] in ("pass", "ok_no_ticks"),
                  timeout_s=5)
    assert ok, f"stream run did not finish: {page.engine.state_for(tid2)}"
    got = page.engine.state_for(tid2)["status"]
    assert got == "pass", f"strict OFF must fold ok_no_ticks into pass, got {got}"

    # --- F. empty matrix + restore ---------------------------------------------------------
    for cb in page._ktype_checks.values():
        cb.setChecked(False)  # each stateChanged -> _rebuild_matrix
    app.processEvents()
    assert len(page._rows) == 0, "unchecking all ktypes must empty the matrix"
    assert page.empty_label.isVisible(), "empty label must be visible with no rows"

    for cb in page._ktype_checks.values():
        cb.setChecked(True)
    app.processEvents()
    assert len(page._rows) == 48, "restoring filters must bring back all 48 rows"

    # --- G. stream live ticks drive the sparkline + tick counter -------------------------------
    fake_lt.stream_status = "pass"
    row3 = next(r for r in page._rows.values() if r.spec.func == "stream_kline")
    tid3 = row3.spec.test_id
    row3.run_btn.click()
    ok = wait_for(lambda: page.engine.state_for(tid3)["status"] == "pass", timeout_s=5)
    assert ok, f"stream run did not pass: {page.engine.state_for(tid3)}"
    st3 = page.engine.state_for(tid3)
    assert st3["ticks"] == 7 and st3["baseline"], st3
    assert row3.spark is not None and len(row3.spark._data) >= 20, (
        "sparkline never received live data"
    )
    assert row3.stream_labels["live_updates"].text().endswith(": 7"), (
        f"tick counter: {row3.stream_labels['live_updates'].text()!r}"
    )

    print(f"calls recorded: {len(fake_lt.calls)} (1 single + 1 batch-of-48 + aborts)")
    print("SMOKE_M3_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

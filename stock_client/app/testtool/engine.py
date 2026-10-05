"""TestEngine — GUI-thread orchestrator for the test matrix.

Owns per-test state, translates worker signals into row updates + summary
counts, and drives single runs / Test All / Abort through the LoopThread
facade. The engine never touches the broker library or widgets directly —
the page listens to its signals and forwards them to the result rows.

State machine per test: pending → running → pass | fail | ok_no_ticks | aborted.
With strict_pass OFF, a stream's `ok_no_ticks` (baseline OK but zero live ticks
— normal outside market hours) is folded into `pass`.
"""

from collections import Counter

from PySide6.QtCore import QObject, Signal


class TestEngine(QObject):
    """Constructed on the GUI thread by the test-tool page."""

    test_state_changed = Signal(str, object)  # test_id, state dict (copy)
    batch_progress = Signal(int, int)          # finished_count, total
    batch_finished = Signal(object)            # summary counts dict

    def __init__(self, loop_thread, parent=None):
        super().__init__(parent)
        self._loop_thread = loop_thread  # LoopThread facade (may predate its worker)
        self._states: dict[str, dict] = {}
        self._batch_total = 0
        self._strict = True
        self._worker_wired = False

    # --- configuration ---------------------------------------------------------
    def set_strict(self, strict: bool):
        self._strict = strict

    def is_busy(self) -> bool:
        """True while a batch is active or any row is still running."""
        if self._batch_total > 0:
            return True
        return any(s["status"] == "running" for s in self._states.values())

    # --- matrix lifecycle --------------------------------------------------------
    def set_matrix(self, specs):
        """(Re)build per-test state when the visible matrix changes.

        Merges rather than replaces: results of rows that survive a filter
        toggle are kept; new rows start pending; removed rows drop out.
        """
        ids = {s.test_id for s in specs}
        self._states = {tid: st for tid, st in self._states.items() if tid in ids}
        for s in specs:
            self._states.setdefault(s.test_id, {"status": "pending"})

    def state_for(self, test_id: str) -> dict | None:
        st = self._states.get(test_id)
        return dict(st) if st is not None else None

    # --- worker signal wiring (once the bridge reports ready) --------------------
    def attach_worker(self, worker):
        """Wire the AsyncWorker's test signals. Called once from worker_ready."""
        if self._worker_wired:
            return
        self._worker_wired = True
        worker.test_started.connect(self._on_test_started)
        worker.test_finished.connect(self._on_test_finished)

    # --- actions (GUI thread) ------------------------------------------------------
    def run_single(self, payload: dict):
        """Run one row. payload keys: test_id/code/ktype/broker/func/kline_num/duration_s."""
        if self._loop_thread is None or self._loop_thread.worker is None:
            return  # bridge not ready — the page keeps Run disabled until then
        tid = payload["test_id"]
        st = self._states.setdefault(tid, {"status": "pending"})
        if st["status"] == "running":
            return  # already in flight
        self._set_status(tid, "running")
        lt = self._loop_thread
        if payload["func"] == "stream_kline":
            lt.run_stream(
                tid, payload["code"], payload["ktype"], payload["broker"],
                payload["kline_num"], payload["duration_s"],
            )
        else:
            lt.run_get_kline(
                tid, payload["code"], payload["ktype"], payload["broker"],
                payload["kline_num"],
            )

    def run_all(self, payloads: list[dict]):
        """Test All — fresh full run of the current matrix (every row → pending)."""
        if self._loop_thread is None or self._loop_thread.worker is None:
            return
        self._states = {p["test_id"]: {"status": "pending"} for p in payloads}
        self._batch_total = len(payloads)
        self.batch_progress.emit(0, self._batch_total)
        self._loop_thread.run_batch(payloads)

    def abort(self):
        """Abort — mark running rows aborted immediately (deterministic UI), then
        cancel on the worker side and wait for broker cleanup there."""
        if self._loop_thread is None:
            return
        for tid, st in list(self._states.items()):
            if st["status"] == "running":
                self._set_status(tid, "aborted")
        self._loop_thread.cancel_all()
        self._check_batch_done()

    # --- worker signal handlers (GUI thread via queued delivery) -------------------
    def _on_test_started(self, test_id: str):
        self._set_status(test_id, "running")

    def _on_test_finished(self, result: dict):
        tid = result.get("test_id")
        st = self._states.get(tid)
        if st is None:
            return  # row left the matrix mid-run (filter toggle) — ignore
        status = result.get("status", "fail")
        if status == "ok_no_ticks" and not self._strict:
            status = "pass"  # non-strict: zero live ticks still counts as pass
        st.update(result)
        st["status"] = status
        self.test_state_changed.emit(tid, dict(st))
        self._check_batch_done()

    def _set_status(self, tid: str, status: str):
        st = self._states.get(tid)
        if st is None:
            return
        st["status"] = status
        self.test_state_changed.emit(tid, dict(st))

    # --- summary --------------------------------------------------------------------
    def summary(self) -> dict:
        c = Counter(s["status"] for s in self._states.values())
        return {
            "pass": c.get("pass", 0),
            "fail": c.get("fail", 0),
            "ok_no_ticks": c.get("ok_no_ticks", 0),
            "aborted": c.get("aborted", 0),
            "pending": c.get("pending", 0),
            "running": c.get("running", 0),
        }

    def _check_batch_done(self):
        if not self._batch_total:
            return
        finished = sum(
            1 for s in self._states.values()
            if s["status"] not in ("pending", "running")
        )
        self.batch_progress.emit(finished, self._batch_total)
        if finished >= self._batch_total:
            self._batch_total = 0
            self.batch_finished.emit(self.summary())

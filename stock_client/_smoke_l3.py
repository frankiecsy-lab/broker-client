"""L3 live end-to-end smoke test — REAL brokers required.

Run:  QT_QPA_PLATFORM=offscreen python _smoke_l3.py   (OpenD + IB Gateway up)
Exits non-zero on any assertion failure; prints SMOKE_L3_OK at the end.

Unlike L0/L2 this touches the real brokers and proves what only live traffic
can prove:
  A. Check Connection -> both probes green (ports verified open first)
  B. get_kline US.AAPL K_DAY via BOTH brokers -> pass with rows > 0
     (exercises IB shared connection + Futu per-call context for real)
  C. short stream window (~15s): Pass during market hours, OK-no-ticks outside —
     either is acceptable; Fail/Aborted is not
  D. clean shutdown releases IB clientId 99: a FRESH bridge re-probes green
     immediately (a leaked session would make the gateway reject with error 1019)

Run this alone — IB allows one session per clientId, so no other app/smoke test
may hold clientId 99 while it runs.
"""

import os
import socket
import sys
from datetime import datetime
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


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def main() -> int:
    # fail fast with a clear message if the gateways are down
    for name, port in (("IB Gateway", 4001), ("Futu OpenD", 11111)):
        if not port_open(port):
            print(f"SKIP: {name} is not listening on 127.0.0.1:{port} — start it and re-run")
            return 2

    app = QApplication(sys.argv)
    app.setApplicationName("Tomato Broker Server")
    app.setOrganizationName("TomatoBrokerServer")

    from app.async_bridge import LoopThread
    from app.settings import AppSettings

    settings = AppSettings()
    saved_mock = bool(settings.tt_mock)
    settings.tt_mock = False  # L3 must hit the real brokers
    broker_dir = str(Path(settings.broker_dir))

    print(f"L3 start: {datetime.now():%Y-%m-%d %H:%M:%S} (local)", flush=True)

    lt = LoopThread(broker_dir=broker_dir)
    finished: list[dict] = []
    lt.start()
    ok_ready = wait_for(lambda: lt.worker is not None, timeout_s=15)
    assert ok_ready, "bridge did not become ready"
    # connect directly on the GUI thread — deterministic, no queued-signal race
    lt.worker.test_finished.connect(lambda r: finished.append(r))

    # --- A. Check Connection -> both green --------------------------------------
    states: list[tuple[str, bool]] = []
    lt.worker.broker_status_changed.connect(lambda b, c: states.append((b, c)))
    lt.probe_all()
    ok_a = wait_for(lambda: {b for b, _ in states} >= {"ib", "futu"}, timeout_s=45)
    assert ok_a, f"probe did not complete (emitted: {states})"
    got = dict(states)
    print(f"A. probes: {got}", flush=True)
    assert got["ib"] is True and got["futu"] is True, f"expected both green: {got}"

    # --- B. get_kline US.AAPL K_DAY via both brokers (sequential) -----------------
    for broker in ("ib", "futu"):
        tid = f"US.AAPL|K_DAY|{broker}|get_kline"
        lt.run_get_kline(tid, "US.AAPL", "K_DAY", broker, 100)
        ok_b = wait_for(lambda: any(r["test_id"] == tid for r in finished), timeout_s=60)
        assert ok_b, f"get_kline [{broker}] did not finish within 60s"
        res = next(r for r in finished if r["test_id"] == tid)
        print(f"B. {broker}: status={res['status']} rows={res.get('rows')} "
              f"elapsed_ms={res.get('elapsed_ms', 0):.0f}", flush=True)
        assert res["status"] == "pass", f"{broker} get_kline failed: {res}"
        assert (res.get("rows") or 0) > 0, f"{broker} returned no rows: {res}"

    # --- C. short stream window (~15s) --------------------------------------------
    tid_s = "US.AAPL|K_1M|ib|stream_kline"
    lt.run_stream(tid_s, "US.AAPL", "K_1M", "ib", 100, 15)
    ok_c = wait_for(lambda: any(r["test_id"] == tid_s for r in finished), timeout_s=60)
    assert ok_c, f"stream did not finish within 60s (finished so far: {len(finished)})"
    res_s = next(r for r in finished if r["test_id"] == tid_s)
    print(f"C. stream: status={res_s['status']} baseline={res_s.get('baseline')} "
          f"ticks={res_s.get('ticks', 0)}", flush=True)
    # market hours -> pass; outside -> ok_no_ticks (both fine); anything else is a bug
    assert res_s["status"] in ("pass", "ok_no_ticks"), f"stream result unacceptable: {res_s}"
    assert res_s.get("baseline") is True, f"stream had no baseline snapshot: {res_s}"

    # --- D. clean shutdown releases IB clientId 99 ---------------------------------
    lt.request_shutdown()
    ok_stop = wait_for(lambda: not lt.isRunning(), timeout_s=20)
    assert ok_stop, "bridge thread did not stop after request_shutdown"
    print("D. first bridge stopped — re-probing with a FRESH bridge (clientId 99 check)",
          flush=True)

    lt2 = LoopThread(broker_dir=broker_dir)
    states2: list[tuple[str, bool]] = []
    lt2.start()
    ok_r2 = wait_for(lambda: lt2.worker is not None, timeout_s=15)
    assert ok_r2, "second bridge did not become ready"
    lt2.worker.broker_status_changed.connect(lambda b, c: states2.append((b, c)))
    lt2.probe_all()
    ok_d = wait_for(lambda: {b for b, _ in states2} >= {"ib", "futu"}, timeout_s=45)
    assert ok_d, f"second probe did not complete (emitted: {states2})"
    got2 = dict(states2)
    print(f"D. re-probe after restart: {got2}", flush=True)
    # a leaked IB session would make the gateway reject clientId 99 -> red dot
    assert got2["ib"] is True, f"IB did not reconnect cleanly (clientId 99 leak?): {got2}"

    lt2.request_shutdown()
    ok_stop2 = wait_for(lambda: not lt2.isRunning(), timeout_s=20)
    assert ok_stop2, "second bridge thread did not stop"

    settings.tt_mock = saved_mock  # restore the user's mock choice
    print("SMOKE_L3_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

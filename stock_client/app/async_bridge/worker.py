"""Async bridge — runs the broker library's asyncio world on a dedicated QThread.

Threading rules (the whole design hinges on these):
- LoopThread.run() creates BOTH the event loop and the AsyncWorker, so the
  worker's QObject thread affinity is the worker thread → every signal it
  emits is auto-queued to GUI-thread slots. The worker NEVER touches widgets.
- BrokerClient is constructed lazily inside a running coroutine (ensure_client)
  because IBClient's asyncio.Lock must be used on one consistent loop.
- Cross-thread payloads are compact plain dicts only — no DataFrames, no
  numpy scalars, no Timestamps cross the boundary.

Shutdown order (request_shutdown): cancel all test tasks → gather their
finally-cleanup (IB removes updateEvent / Futu closes its context) →
BrokerClient.__aexit__ (releases IB clientId 99) → stop loop → thread exits.
"""

import asyncio
import logging
import time
from contextlib import contextmanager

from PySide6.QtCore import QObject, QThread, Signal

from ..broker_access import create_client


def _plain(value):
    """Convert a pandas/numpy scalar to a native Python type for the wire."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Timestamp / datetime → display string (space-separated, like pandas)
    if hasattr(value, "isoformat"):
        try:
            return str(value)
        except Exception:
            pass
    item = getattr(value, "item", None)  # numpy scalar → python scalar
    if callable(item):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


class AsyncWorker(QObject):
    """Lives on the worker thread. Emits signals only; never touches widgets."""

    test_started = Signal(str)                 # test_id
    stream_tick = Signal(str, object)          # test_id, compact payload dict (M4)
    test_finished = Signal(object)             # full result dict
    broker_status_changed = Signal(str, bool)  # "ib"|"futu", connected?
    status_message = Signal(str)               # transient message for the strip

    def __init__(self, broker_dir: str, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.broker_dir = broker_dir
        self._loop = loop
        self._client = None          # BrokerClient (lazy; built on this loop)
        self._mock_client = None     # MockBrokerClient while the mock toggle is ON (M5)
        self._client_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()   # active test tasks (abort target, M3)
        self._shutting_down = False

    # --- lifecycle -----------------------------------------------------------
    def use_mock(self, enabled: bool, tick_count: int = 5):
        """GUI-thread entry point — swap in / remove the offline MockBrokerClient (M5).

        Toggling off drops the mock so the next test builds the real client. A
        previously built real client is left alone (it stays connected and reused).
        """
        if enabled:
            from ..testtool.mock_broker import MockBrokerClient  # lazy: pandas-only, no broker SDKs
            self._mock_client = MockBrokerClient(tick_count=tick_count)
        else:
            self._mock_client = None

    async def ensure_client(self):
        """Return the active client — mock when toggled on, else build the shared BrokerClient."""
        if self._mock_client is not None:
            return self._mock_client
        async with self._client_lock:
            if self._client is None:
                self._client = create_client(self.broker_dir)
                await self._client.__aenter__()  # no-op today; keeps lifecycle symmetric
        return self._client

    async def shutdown(self):
        """Cancel everything, release brokers, stop the loop. Idempotent."""
        if self._shutting_down:
            return
        self._shutting_down = True
        tasks = [t for t in self._tasks if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)  # releases IB clientId 99
            except Exception as e:
                logging.warning("BrokerClient cleanup failed: %s", e)
            self._client = None
        if self._loop is not None and self._loop.is_running():
            self._loop.stop()

    # --- scheduling helper (called from the GUI thread) ----------------------
    def _schedule(self, coro):
        loop = self._loop
        if self._shutting_down or loop is None or not loop.is_running():
            coro.close()  # avoid "coroutine never awaited" warnings
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError as e:  # loop stopped between check and call
            coro.close()
            logging.warning("schedule rejected (loop not running): %s", e)

    @contextmanager
    def _track(self, task: asyncio.Task):
        """Register a running test task so shutdown/abort can cancel it (M3)."""
        self._tasks.add(task)
        try:
            yield
        finally:
            self._tasks.discard(task)

    # --- connection probes (status-bar dots) ---------------------------------
    def probe_all(self):
        """GUI-thread entry point — check IB and Futu in parallel."""
        self._schedule(self._probe_all())

    async def _probe_all(self):
        try:
            await self.ensure_client()  # raises if the broker library is missing
        except Exception as e:
            logging.warning("broker library unavailable: %s", e)
            self.status_message.emit(f"broker library unavailable: {e}")
            return
        ib_ok, futu_ok = await asyncio.gather(
            self._probe_one("ib"), self._probe_one("futu")
        )
        self.broker_status_changed.emit("ib", ib_ok)
        self.broker_status_changed.emit("futu", futu_ok)

    async def _probe_one(self, broker: str) -> bool:
        try:
            client = await self.ensure_client()
        except Exception as e:
            logging.warning("ensure_client failed during probe [%s]: %s", broker, e)
            return False

        if broker == "ib":
            # Shared persistent connection (clientId 99): probing LEAVES it open —
            # that is intended, subsequent tests reuse the same socket. A client
            # without an IB side (e.g. the offline mock) simply reports down.
            ib = getattr(client, "ib_client", None)
            if ib is None:
                return False
            try:
                await ib._ensure_connected()
                return bool(ib.ib is not None and ib.ib.isConnected())
            except Exception as e:
                logging.warning("IB probe failed: %s", e)
                try:
                    await ib.disconnect()  # drop a half-open connection so the next probe retries clean
                except Exception:
                    pass
                return False

        # futu — per-call context; run in a thread so a hung OpenD can't stall the loop
        def _futu_check():
            import futu  # installed package (futu-api), not from broker_dir
            ctx = client.futu_client._new_ctx()
            try:
                ret, _msg = ctx.get_global_state()
                return int(ret) == int(futu.RET_OK)
            finally:
                ctx.close()

        try:
            return await asyncio.to_thread(_futu_check)
        except Exception as e:
            logging.warning("Futu probe failed: %s", e)
            return False

    # --- get_kline test (M2; stream flow lands in M4) -------------------------
    def run_get_kline(self, test_id: str, code: str, ktype: str, broker: str, kline_num: int):
        """GUI-thread entry point for a single get_kline test."""
        self._schedule(
            self._run_get_kline(test_id, code, ktype, broker, kline_num)
        )

    async def _run_get_kline(self, test_id, code, ktype, broker, kline_num):
        task = asyncio.current_task()
        self.test_started.emit(test_id)
        t0 = time.perf_counter()
        result = {
            "test_id": test_id, "code": code, "ktype": ktype,
            "broker": broker, "func": "get_kline",
        }
        try:
            with self._track(task):  # register for abort (M3) + shutdown
                client = await self.ensure_client()
                status, data, message = await client.get_kline(
                    code=code, ktype=ktype, broker=broker, kline_num=kline_num
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                if not status or data is None:
                    result.update(status="fail", error=str(message), rows=0,
                                  elapsed_ms=elapsed_ms)
                else:
                    df = self._normalize_for_display(data)
                    preview = [
                        {k: _plain(v) for k, v in row.items()}
                        for row in df.tail(20).to_dict("records")
                    ]
                    result.update(
                        status="pass", rows=len(df), elapsed_ms=elapsed_ms,
                        preview=preview, last_bar=(preview[-1] if preview else None),
                    )
        except asyncio.CancelledError:
            raise  # engine marks it 'aborted' (M3)
        except Exception as e:
            logging.exception("get_kline test failed [%s]", test_id)
            result.update(status="fail", error=f"{type(e).__name__}: {e}", rows=0,
                          elapsed_ms=(time.perf_counter() - t0) * 1000.0)
        self.test_finished.emit(result)

    @staticmethod
    def _normalize_for_display(df):
        """pd.to_datetime-normalize time_key (IB datetime64 vs Futu string)."""
        import pandas as pd
        df = df.copy()
        try:
            df["time_key"] = pd.to_datetime(df["time_key"])
        except Exception:
            pass  # leave raw values; the preview still renders something
        return df

    # --- stream_kline test -------------------------------------------------------
    def run_stream(self, test_id: str, code: str, ktype: str, broker: str,
                   kline_num: int, duration_s: int):
        """GUI-thread entry point for a single stream_kline test."""
        self._schedule(
            self._run_stream(test_id, code, ktype, broker, kline_num, duration_s)
        )

    async def _run_stream(self, test_id, code, ktype, broker, kline_num, duration_s):
        """Consume the stream for `duration_s` seconds, then stop it cleanly.

        The consumer task runs `async for df in gen`; the FIRST yield is the
        history baseline, every later yield counts as a live tick. Stopping =
        cancel the consumer: the CancelledError propagates into the broker
        generator's own except/finally (IB removes updateEvent / Futu closes its
        context), so no subscription leaks. Live UI updates are throttled to
        ~3Hz via stream_tick with compact plain-dict payloads only.

        Pass criteria: baseline + >=1 tick = pass; baseline + 0 ticks =
        ok_no_ticks (normal outside market hours); no baseline = fail.
        """
        task = asyncio.current_task()
        self.test_started.emit(test_id)
        t0 = time.perf_counter()
        st = {"baseline": False, "ticks": 0, "first_ts": None, "last_ts": None,
              "closes": [], "last_bar": None, "error": None}
        consumer: asyncio.Task | None = None
        cancelled = False
        try:
            with self._track(task):
                client = await self.ensure_client()
                gen = client.stream_kline(
                    code=code, ktype=ktype, broker=broker, kline_num=kline_num
                )

                async def consume():
                    last_emit = 0.0
                    try:
                        async for df in gen:
                            if not st["baseline"]:
                                st["baseline"] = True
                                st["closes"] = [float(x) for x in df["close"].tolist()][-200:]
                                st["last_bar"] = self._bar_dict(df)
                                self.stream_tick.emit(test_id, self._tick_payload(st))
                            else:
                                st["ticks"] += 1
                                ts = _plain(df["time_key"].iloc[-1])
                                if st["first_ts"] is None:
                                    st["first_ts"] = ts
                                st["last_ts"] = ts
                                st["closes"].append(float(df["close"].iloc[-1]))
                                st["closes"] = st["closes"][-200:]
                                st["last_bar"] = self._bar_dict(df)
                            now = time.monotonic()
                            if now - last_emit >= 0.3:  # ~3Hz throttle for the sparkline
                                self.stream_tick.emit(test_id, self._tick_payload(st))
                                last_emit = now
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        st["error"] = f"{type(e).__name__}: {e}"

                consumer = asyncio.create_task(consume())
                sleep_t = asyncio.ensure_future(asyncio.sleep(duration_s))
                try:
                    # End early if the stream dies (e.g. history fetch failed);
                    # otherwise wait out the full observation window.
                    await asyncio.wait({consumer, sleep_t},
                                       return_when=asyncio.FIRST_COMPLETED)
                finally:
                    if not sleep_t.done():
                        sleep_t.cancel()
        except asyncio.CancelledError:
            cancelled = True  # abort — fall through to cleanup, then exit quietly
        finally:
            if consumer is not None and not consumer.done():
                consumer.cancel()

        # Wait for broker-side cleanup (IB updateEvent removal / Futu ctx.close)
        # BEFORE this task completes, so shutdown can safely release the client.
        if consumer is not None:
            try:
                await consumer
            except asyncio.CancelledError:
                pass  # expected on both the window-elapsed and abort paths

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        result = {"test_id": test_id, "code": code, "ktype": ktype, "broker": broker,
                  "func": "stream_kline", "elapsed_ms": elapsed_ms,
                  "baseline": st["baseline"], "ticks": st["ticks"]}
        if cancelled:
            return  # engine already marked this row 'aborted' — no emit
        if not st["baseline"]:
            if st["error"]:
                result.update(status="fail", error=st["error"])  # broker-side message, raw
            else:
                result.update(status="fail", error_key="err_no_baseline")  # translated in the row
        elif st["ticks"] == 0:
            result.update(status="ok_no_ticks", closes=list(st["closes"]),
                          last_bar=st["last_bar"])
        else:
            result.update(status="pass", first_ts=st["first_ts"], last_ts=st["last_ts"],
                          closes=list(st["closes"]), last_bar=st["last_bar"])
        self.test_finished.emit(result)

    @staticmethod
    def _bar_dict(df):
        """Last row of a kline DataFrame as a plain dict (wire-safe)."""
        try:
            row = df.iloc[[-1]].to_dict("records")[0]
            return {k: _plain(v) for k, v in row.items()}
        except Exception:
            return None

    @staticmethod
    def _tick_payload(st):
        """Compact live payload for stream_tick — plain floats/dicts only."""
        return {"ticks": st["ticks"], "last_bar": st["last_bar"],
                "closes": list(st["closes"])}

    # --- batch runner (Test All) + abort ------------------------------------------
    MAX_CONCURRENT = 4  # cap on simultaneous broker calls (IB shared connection / OpenD load)

    def run_batch(self, specs: list[dict]):
        """GUI-thread entry point for Test All.

        specs: [{'test_id','code','ktype','broker','func','kline_num','duration_s'}]
        Tasks are created in spec order (get_kline rows first — see build_matrix)
        and share one semaphore, so the queue is strictly FIFO: all get_klines
        finish before any stream starts.
        """
        self._schedule(self._run_batch(specs))

    async def _run_batch(self, specs):
        sem = asyncio.Semaphore(self.MAX_CONCURRENT)

        async def one(spec: dict):
            async with sem:
                if spec["func"] == "stream_kline":
                    await self._run_stream(
                        spec["test_id"], spec["code"], spec["ktype"], spec["broker"],
                        spec["kline_num"], spec["duration_s"],
                    )
                else:
                    await self._run_get_kline(
                        spec["test_id"], spec["code"], spec["ktype"], spec["broker"],
                        spec["kline_num"],
                    )

        tasks = [asyncio.create_task(one(s)) for s in specs]
        # Inner test tasks register themselves via _track (abort target); the
        # wrappers need no registry entry — gather just observes them.
        await asyncio.gather(*tasks, return_exceptions=True)

    def cancel_all(self):
        """GUI-thread entry point — abort every active test (Abort button)."""
        self._schedule(self._cancel_all())

    async def _cancel_all(self):
        tasks = [t for t in self._tasks if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            # Await so broker cleanup (IB updateEvent / Futu ctx) completes before
            # the GUI is told everything stopped.
            await asyncio.gather(*tasks, return_exceptions=True)


class LoopThread(QThread):
    """Owns the event loop + AsyncWorker. GUI-thread facade over the worker."""

    worker_ready = Signal(object)  # emits the AsyncWorker once it exists on this thread

    def __init__(self, broker_dir: str, parent=None):
        super().__init__(parent)
        self.broker_dir = broker_dir
        self._worker: AsyncWorker | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_requested = False

    @property
    def worker(self) -> AsyncWorker | None:
        return self._worker

    # --- QThread body (runs on the worker thread) ----------------------------
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # Worker is constructed HERE so its signal affinity == this thread.
        self._worker = AsyncWorker(broker_dir=self.broker_dir, loop=loop)
        try:
            self.worker_ready.emit(self._worker)
            if self._stop_requested:
                # Shutdown raced ahead of us — drain and exit without entering run_forever.
                loop.run_until_complete(self._worker.shutdown())
                return
            loop.run_forever()
        finally:
            # Safety net for tasks still pending when the loop stops (e.g. shutdown
            # was never requested): cancel + drain so broker cleanup runs.
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                try:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                except Exception:
                    pass
            loop.close()

    # --- GUI-thread facade (safe to call at any time) -------------------------
    def probe_all(self):
        w = self._worker
        if w is not None:
            w.probe_all()
        else:
            logging.info("probe_all ignored — bridge not ready yet")

    # Test-runner facades — no-op (with a log) until the worker exists on this thread.
    def run_get_kline(self, test_id, code, ktype, broker, kline_num):
        w = self._worker
        if w is not None:
            w.run_get_kline(test_id, code, ktype, broker, kline_num)

    def run_stream(self, test_id, code, ktype, broker, kline_num, duration_s):
        w = self._worker
        if w is not None:
            w.run_stream(test_id, code, ktype, broker, kline_num, duration_s)

    def run_batch(self, specs):
        w = self._worker
        if w is not None:
            w.run_batch(specs)

    def cancel_all(self):
        w = self._worker
        if w is not None:
            w.cancel_all()

    def use_mock(self, enabled: bool, tick_count: int = 5):
        """GUI-thread facade — swap in / remove the offline mock client (M5)."""
        w = self._worker
        if w is not None:
            w.use_mock(enabled, tick_count)

    def request_shutdown(self):
        """GUI-thread entry point; pair with wait().

        Cancels tests, releases brokers (IB clientId 99), stops the loop.
        Polls briefly so a close that races thread startup still lands —
        without this, closing within ~1s of launch would stall on wait(5000).
        """
        deadline = time.monotonic() + 2.0
        while True:
            worker = self._worker
            loop = self._loop
            if worker is not None and loop is not None and loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(worker.shutdown(), loop)
                except RuntimeError as e:  # loop stopped between check and call
                    logging.warning("request_shutdown rejected: %s", e)
                return
            if not self.isRunning():
                return  # thread already finished — nothing to do
            self._stop_requested = True  # run() checks this before entering run_forever
            if time.monotonic() >= deadline:
                logging.warning("request_shutdown timed out waiting for the bridge")
                return
            time.sleep(0.01)

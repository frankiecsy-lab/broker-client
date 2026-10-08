# -*- coding: utf-8 -*-
"""K 線 stream 嘅共用基建：一條 `QThread` 擁有 asyncio event loop + 一個 worker `QObject`。

K 線頁 / 行情頁原本各自寫咗一份**完全一樣**嘅 loop 生命週期同 client holder。呢檔只收呢兩樣嘢：

* `LoopThreadBase` — 起 loop、喺**呢條 thread** 建 worker（signal affinity 啱 → emit 自動 queue 去 GUI）、
  `worker_ready`、`request_shutdown()`（2 秒 deadline poll，應付「未起好就收線」嘅 race）。
* `ClientHolderMixin` — worker 側 lazy 開 `BrokerClient` / 收線 `__aexit__`，`client_factory` 俾 E2E 注入 fake。

⚠️ **worker 嘅 payload 形狀照樣各管各**（K 線頁 send records 俾 ResultPanel、行情頁 send df 俾 chart）
→ 所以用 mixin 而唔係共用 `QObject` base（base 會逼埋 signal 形狀一齊改）。
⚠️ 呢檔唔准 import `gateway.pages.*`：兩頁都要 import 佢 → 會循環。
"""
import asyncio
import logging
import time

from PySide6.QtCore import QThread, Signal


class ClientHolderMixin:
    """worker 嘅 client 生命週期。`client_factory()` 返 None → 用真 `BrokerClient`（late-bind，E2E 先至塞得入 fake）。"""

    def init_client_holder(self, loop, client_factory=None):
        self._loop = loop
        self._client_factory = client_factory
        self._client = None
        self._client_lock = asyncio.Lock()   # 並發首調 ensure_client 序列化（IB clientId 安全）
        self._shutting_down = False

    async def ensure_client(self):
        async with self._client_lock:
            if self._client is None:
                client = self._client_factory() if self._client_factory is not None else None
                if client is None:   # late-bind factory 而家返 None → 真 BrokerClient
                    from modules.broker import BrokerClient   # lazy — import 拖慢 UI 啟動
                    client = BrokerClient()
                self._client = client
                await self._client.__aenter__()
        return self._client

    async def _cancel_work(self):
        """subclass 必須實現：cancel 晒自己所有 consumer task（broker 端 cleanup 完成先算數）。"""

    async def release_client(self):
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)   # 釋放 IB clientId / futu 共享 ctx
            except Exception as e:
                logging.warning('broker cleanup failed: %s', e)
            self._client = None

    async def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        await self._cancel_work()
        await self.release_client()
        if self._loop.is_running():
            self._loop.stop()   # run_forever 返嚟 → LoopThreadBase.run() 先可以行 finally


class LoopThreadBase(QThread):
    """擁有 event loop + worker；GUI thread 嘅 facade。"""

    worker_ready = Signal(object)

    worker_cls = None   # subclass 指定 worker 類別

    def __init__(self, client_factory=None, parent=None):
        super().__init__(parent)
        self._client_factory = client_factory
        self._worker = None
        self._loop = None
        self._stop_requested = False

    @property
    def worker(self):
        return self._worker

    def make_worker(self, loop):
        return self.worker_cls(loop, self._client_factory)

    # --- QThread body（跑喺 worker thread） ------------------------------------
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # worker 喺呢度建立 → signal affinity == 呢個 thread → emit 自動 queue 去 GUI
        self._worker = self.make_worker(loop)
        try:
            self.worker_ready.emit(self._worker)
            if self._stop_requested:   # 未入 run_forever 已被叫收線
                loop.run_until_complete(self._worker.shutdown())
                return
            loop.run_forever()
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                try:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            loop.close()

    # --- GUI-thread facade ------------------------------------------------------
    def request_shutdown(self):
        """GUI-thread 入口；pair with wait()。

        ⚠️ 要 poll：`start()` 之後 worker/loop 可能仲未起好，呢一刻直接 return 會搞到 `wait()` 一直等住
           `run_forever`（永遠冇人 stop 佢）。標 `_stop_requested` 等 `run()` 自己收線，最長 2 秒。
        """
        deadline = time.monotonic() + 2.0
        while True:
            w, loop = self._worker, self._loop
            if w is not None and loop is not None and loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(w.shutdown(), loop)
                except RuntimeError as e:
                    logging.warning('request_shutdown rejected: %s', e)
                return
            if not self.isRunning():
                return
            self._stop_requested = True
            if time.monotonic() >= deadline:
                logging.warning('request_shutdown timed out waiting for the bridge')
                return
            time.sleep(0.01)

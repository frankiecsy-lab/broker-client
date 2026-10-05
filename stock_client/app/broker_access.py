"""Bootstrap the external broker library (D:\\coding\\broker_client) at runtime.

The GUI never hard-codes the import path: settings.broker_dir points at the
library folder, we insert it into sys.path and import `broker` lazily so a
missing/moved library degrades to an error message instead of a crash at
startup. Note: broker_dir is read once per process — changing it in settings
takes effect on next launch (module cache).
"""

import importlib
import logging
import sys
from pathlib import Path


def ensure_broker_module(broker_dir: str) -> str:
    """Insert broker_dir into sys.path (idempotent); return the resolved path.

    Raises FileNotFoundError when the folder or its broker.py is missing —
    callers surface this as a status-bar message, never an unhandled crash.
    """
    p = Path(broker_dir).expanduser().resolve()
    if not (p / "broker.py").is_file():
        raise FileNotFoundError(f"broker.py not found in {p}")
    key = str(p)
    if key not in sys.path:
        sys.path.insert(0, key)
        logging.info("broker library path added to sys.path: %s", key)
    return key


def create_client(broker_dir: str):
    """Instantiate BrokerClient from the broker library.

    MUST be called on the worker thread with its event loop running —
    IBClient builds an asyncio.Lock that binds to the current loop, so a
    client constructed on the GUI thread would deadlock on first use.
    """
    ensure_broker_module(broker_dir)
    mod = importlib.import_module("broker")
    return mod.BrokerClient()
